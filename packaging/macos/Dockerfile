# Builds hubdeck.app for macOS on Linux. From the repository root:
#
#   docker build --platform linux/amd64 -t hubdeck-macos-builder -f packaging/macos/Dockerfile packaging/macos
#   docker run --rm --platform linux/amd64 -v "$PWD:/src" -w /src hubdeck-macos-builder
#
# -> dist/hubdeck-<version>-macos-arm64.zip. Signing: pass MACOS_SIGN_P12 (a path inside /src),
# MACOS_SIGN_P12_PASSWORD and MACOS_NOTARY_API_KEY with -e, see build.py.
FROM python:3.13.7-slim-bookworm

COPY install_tools.py /tmp/install_tools.py
RUN python /tmp/install_tools.py && rm /tmp/install_tools.py

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
CMD ["python", "packaging/macos/build.py", "--arch", "arm64"]
