"""Fixed private visit child; exit status alone is not a saved response."""

import sys

from clinic_photos.publication import process, visit_annotation_child

EXIT_REFUSED = 2
EXIT_UNAVAILABLE = 3
EXIT_VERIFIED = 0


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (len(arguments) != 4 or arguments[0] != "--site"
            or arguments[2] != "--operation-id"
            or not process._valid_visit_site(arguments[1])
            or not process._valid_operation_id(arguments[3])):
        return EXIT_REFUSED
    try:
        with visit_annotation_child._bootstrap(arguments[3], site=arguments[1]) as proof:
            import frappe
            from clinic_photos.publication import exclusion, visit_annotation_coordinator

            site, operation = arguments[1], arguments[3]
            actor = getattr(frappe.session, "user", None)
            visit_annotation_child._consume(proof, site=site, actor=actor, operation_id=operation)
            with exclusion.hold_visit_annotation_exclusion(operation) as owner:
                if (owner.site != site or owner.actor != actor or
                        owner.operation_id != operation):
                    return EXIT_UNAVAILABLE
                result = visit_annotation_coordinator._publish_verified(owner)
                if (type(result) is not visit_annotation_coordinator._Verified or
                        result.operation != operation):
                    return EXIT_UNAVAILABLE
    except Exception:
        return EXIT_UNAVAILABLE
    return EXIT_VERIFIED


if __name__ == "__main__":
    sys.exit(main())
