"""Test-only visit child wrapper; inert on import, never modifies application code."""

import argparse
import dis
import importlib.util
import inspect
import math
import os
import re
import stat
import sys
import time
from pathlib import Path

SITE = "marley-photo-acceptance.local"
MAX_TIMEOUT = 30.0


class BarrierRefused(RuntimeError):
    pass


def wait_for_path(path, timeout):
    """Bounded helper for offline tests, not a site synchronization primitive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.005)
    return False


def run_with_barrier(function, line, operation_id, site, directory, timeout, runner):
    if (site != SITE or not isinstance(operation_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None
        or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT
        or line not in {number for _, number in dis.findlinestarts(function.__code__)}):
        raise BarrierRefused("invalid barrier parameters")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_fd = os.open(directory, flags)
    except OSError:
        raise BarrierRefused("private directory unavailable") from None
    previous = sys.gettrace()
    hits = 0
    failed = False
    try:
        info = os.fstat(directory_fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise BarrierRefused("private directory required")
        for name in ("ready", "release"):
            try:
                os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise BarrierRefused("sentinel already exists")

        def trace(frame, event, arg):
            nonlocal hits, failed
            if frame.f_code is not function.__code__:
                return None
            if event != "line" or frame.f_lineno != line:
                return trace
            try:
                prepared = frame.f_locals.get("prepared")
                if (hits or getattr(prepared, "id", None) != operation_id
                    or getattr(prepared, "site", None) != site
                    or getattr(prepared, "state", None) != "PREPARED"):
                    raise BarrierRefused("operation mismatch or repeated boundary")
                hits += 1
                fd = os.open("ready", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
                os.close(fd)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        release = os.stat("release", dir_fd=directory_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        time.sleep(0.01)
                        continue
                    if (not stat.S_ISREG(release.st_mode) or release.st_uid != os.geteuid()
                        or stat.S_IMODE(release.st_mode) != 0o600 or release.st_nlink != 1):
                        raise BarrierRefused("invalid release sentinel")
                    return trace
                raise BarrierRefused("barrier timed out")
            except BaseException:
                failed = True
                raise

        sys.settrace(trace)
        result = runner()
        if failed or hits != 1:
            raise BarrierRefused("barrier not completed")
        return result
    finally:
        sys.settrace(previous)
        os.close(directory_fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True, choices=[SITE])
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    from clinic_photos.publication import coordinator, process

    function = coordinator._publish_prepared_visit
    path = Path(inspect.getsourcefile(function)).resolve(strict=True)
    if path != Path(coordinator.__file__).resolve(strict=True):
        raise BarrierRefused("coordinator source mismatch")
    source = path.read_text()
    compiled = compile(source, str(path), "exec", dont_inherit=True)
    matching = [item for item in compiled.co_consts
                if inspect.iscode(item) and item.co_name == "_publish_prepared_visit"]
    if (len(matching) != 1 or matching[0].co_code != function.__code__.co_code
        or matching[0].co_linetable != function.__code__.co_linetable
        or matching[0].co_consts != function.__code__.co_consts):
        raise BarrierRefused("loaded coordinator differs from source")
    spec = importlib.util.spec_from_file_location(
        "visit_precommit_probe", Path(__file__).with_name("visit_precommit_probe.py")
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    boundary = probe.find_boundary(source)
    return run_with_barrier(
        function, boundary["commit_line"], args.operation_id, args.site, args.directory,
        args.timeout, lambda: process.run_visit(args.operation_id, site=args.site),
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BarrierRefused:
        print("test barrier refused", file=sys.stderr)
        sys.exit(2)
