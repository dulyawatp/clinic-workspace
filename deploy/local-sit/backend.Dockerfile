# syntax=docker/dockerfile:1
#
# Backend image for the isolated local SIT stack.
#
# Derived from the pinned official Frappe/ERPNext image (see README for the
# verified versions inside the image), then adds:
#   - Marley Healthcare at a pinned tag and enforced commit,
#   - the repository's clinic_ui and clinic_photos apps (source only),
#   - static asset links so Frappe serves /assets/<app>/ from the app's public/.
#
# The native React bundle is built on the host before this image is built
# (`pnpm --filter @clinic/app build:native`) and is copied in as part of
# clinic_ui/clinic_ui/public/ui.
#
# Base image is pinned by immutable digest. The image tag names ERPNext only;
# the Frappe framework version inside it is asserted separately below.
FROM frappe/erpnext:v16.52.1@sha256:88654c9f5fb8e47266b492932270239d4d7c3edd4af3966d0768616c0cd20a42

ARG MARLEY_REPO=https://github.com/earthians/marley
ARG MARLEY_REF=v16.6.1
ARG MARLEY_COMMIT=dda107b26e3a0fe1dbd65c7257371235de054a04
ARG EXPECTED_FRAPPE_VERSION=16.54.0
ARG EXPECTED_ERPNEXT_VERSION=16.52.1
ARG CLINIC_SOURCE_REV=unknown

LABEL org.opencontainers.image.title="marley-sit backend" \
      org.opencontainers.image.base.name="docker.io/frappe/erpnext:v16.52.1" \
      org.opencontainers.image.base.digest="sha256:88654c9f5fb8e47266b492932270239d4d7c3edd4af3966d0768616c0cd20a42" \
      org.opencontainers.image.revision="${CLINIC_SOURCE_REV}" \
      com.marley.sit.marley-ref="${MARLEY_REF}" \
      com.marley.sit.marley-commit="${MARLEY_COMMIT}"

USER frappe
WORKDIR /home/frappe/frappe-bench

# Fail closed if the base image versions are not the expected Frappe 16 /
# ERPNext 16 pair recorded for this deployment.
RUN test "$(./env/bin/python -c 'import frappe;print(frappe.__version__)')" = "${EXPECTED_FRAPPE_VERSION}" \
 && test "$(./env/bin/python -c 'import erpnext;print(erpnext.__version__)')" = "${EXPECTED_ERPNEXT_VERSION}"

# Marley app (package name: healthcare). Pinned tag, enforced commit, shallow
# clone; the commit is recorded before the git metadata is dropped.
RUN git clone --depth 1 --branch "${MARLEY_REF}" "${MARLEY_REPO}" apps/healthcare \
 && test "$(git -C apps/healthcare rev-parse HEAD)" = "${MARLEY_COMMIT}" \
 && git -C apps/healthcare rev-parse HEAD > apps/healthcare/.sit-commit \
 && ./env/bin/pip install --no-cache-dir -e apps/healthcare \
 && rm -rf apps/healthcare/.git

COPY --chown=frappe:0 frappe-apps/clinic_ui /home/frappe/frappe-bench/apps/clinic_ui
COPY --chown=frappe:0 frappe-apps/clinic_photos /home/frappe/frappe-bench/apps/clinic_photos

RUN ./env/bin/pip install --no-cache-dir -e apps/clinic_ui -e apps/clinic_photos

# Static app assets: Frappe maps /assets/<app> to sites/assets/<app>, and at
# container start sites/assets is a symlink to this baked assets directory.
RUN ln -sfn /home/frappe/frappe-bench/apps/healthcare/healthcare/public assets/healthcare \
 && ln -sfn /home/frappe/frappe-bench/apps/clinic_ui/clinic_ui/public assets/clinic_ui \
 && ln -sfn /home/frappe/frappe-bench/apps/clinic_photos/clinic_photos/public assets/clinic_photos

USER root
RUN mkdir -p /opt/sit \
 && { \
      echo "base_image=frappe/erpnext:v16.52.1"; \
      echo "base_digest=sha256:88654c9f5fb8e47266b492932270239d4d7c3edd4af3966d0768616c0cd20a42"; \
      echo "frappe_version=${EXPECTED_FRAPPE_VERSION}"; \
      echo "erpnext_version=${EXPECTED_ERPNEXT_VERSION}"; \
      echo "marley_repo=${MARLEY_REPO}"; \
      echo "marley_ref=${MARLEY_REF}"; \
      echo "marley_commit=$(cat /home/frappe/frappe-bench/apps/healthcare/.sit-commit)"; \
      echo "clinic_source_rev=${CLINIC_SOURCE_REV}"; \
    } > /opt/sit/revisions.txt \
 && rm -f /home/frappe/frappe-bench/apps/healthcare/.sit-commit \
 && chmod 0644 /opt/sit/revisions.txt
USER frappe
