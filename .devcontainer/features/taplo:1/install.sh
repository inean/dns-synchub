#!/usr/bin/env bash
set -e

echo "Installing Taplo..."

# Ensure the script is run as root
if [ "$(id -u)" -ne 0 ]; then
    echo -e 'Script must be run as root. Use sudo, su, or add "USER root" to your Dockerfile before running this script.'
    exit 1
fi

# Detect OS and architecture

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
# Normalize OS name
if [[ "$OS" == "darwin" ]]; then
    OS="macos"
elif [[ "$OS" == "linux" ]]; then
    OS="linux"
else
    echo "Unsupported OS: $OS"
    exit 1
fi

ARCH="$(uname -m)"
# Normalize architecture name
case "$ARCH" in
    x86_64|amd64)
        ARCH="x86_64"
        ;;
    arm64|aarch64)
        ARCH="aarch64"
        ;;
    *)
        echo "Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

# Download and install Taplo

# Set the TAPLO_URL
TAPLO_URL="https://github.com/tamasfe/taplo/releases/latest/download/taplo-full-$OS-$ARCH.gz"

# Download the file
curl -fsSL "$TAPLO_URL" -o /tmp/taplo.gz

# Unzip the file
gzip -d /tmp/taplo.gz

# Install the binary
install -m 755 /tmp/taplo /usr/local/bin/taplo

echo "Taplo installation completed."
