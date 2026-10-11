# Builds hubdeck for Windows on Linux. From the repository root (PowerShell: "${PWD}:/src"):
#
#   docker build -t hubdeck-windows-builder -f packaging/windows/Dockerfile packaging/windows
#   docker run --rm -v "$PWD:/src" -w /src hubdeck-windows-builder
#
# -> dist/hubdeck-<version>-windows-x64.msi and .zip
#
# Debian trixie for wixl 0.106 (bookworm's 0.101 can't put hubdeck on the PATH). git names local
# builds after the branch; safe.directory because the mounted checkout belongs to another user.
FROM python:3.13.7-slim-trixie

RUN apt-get update && apt-get install -y --no-install-recommends wixl git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
RUN pip install --no-cache-dir --root-user-action=ignore ziglang==0.16.0 "pillow>=10.1" "packaging>=24"

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
CMD ["python", "packaging/windows/build.py"]
