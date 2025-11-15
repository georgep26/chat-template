#!/bin/bash

# Detect if script is being sourced or executed
# When sourced, ${BASH_SOURCE[0]} != $0
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    IS_SOURCED=true
else
    IS_SOURCED=false
    set -e  # Exit on error only when executed, not when sourced
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to exit or return based on how script is invoked
script_exit() {
    local exit_code=${1:-1}
    if [[ "$IS_SOURCED" == "true" ]]; then
        return $exit_code
    else
        exit $exit_code
    fi
}

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "linux"
    elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        echo "windows"
    else
        echo "unknown"
    fi
}

# Check if conda is installed
check_conda() {
    if command -v conda &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# Install conda on macOS
install_conda_macos() {
    print_info "Installing Miniconda on macOS..."
    
    if command -v brew &> /dev/null; then
        print_info "Using Homebrew to install Miniconda..."
        brew install --cask miniconda
    else
        print_warn "Homebrew not found. Installing Miniconda via direct download..."
        local installer="Miniconda3-latest-MacOSX-x86_64.sh"
        local url="https://repo.anaconda.com/miniconda/${installer}"
        
        cd /tmp
        curl -O "${url}"
        bash "${installer}" -b -p "${HOME}/miniconda3"
        rm "${installer}"
        
        # Initialize conda for bash
        "${HOME}/miniconda3/bin/conda" init bash
        source "${HOME}/.bash_profile" 2>/dev/null || true
        
        print_info "Miniconda installed. Please restart your terminal or run: source ~/.bash_profile"
    fi
}

# Install conda on Linux
install_conda_linux() {
    print_info "Installing Miniconda on Linux..."
    
    local installer="Miniconda3-latest-Linux-x86_64.sh"
    local url="https://repo.anaconda.com/miniconda/${installer}"
    
    cd /tmp
    curl -O "${url}"
    bash "${installer}" -b -p "${HOME}/miniconda3"
    rm "${installer}"
    
    # Initialize conda for bash
    "${HOME}/miniconda3/bin/conda" init bash
    source "${HOME}/.bashrc" 2>/dev/null || true
    
    print_info "Miniconda installed. Please restart your terminal or run: source ~/.bashrc"
}

# Install conda on Windows (Git Bash/Cygwin)
install_conda_windows() {
    print_error "Windows installation via script is not fully supported."
    print_info "Please install Miniconda manually from: https://docs.conda.io/en/latest/miniconda.html"
    print_info "After installation, restart your terminal and run this script again."
    script_exit 1
}

# Main installation function
install_conda() {
    local os=$(detect_os)
    
    case "$os" in
        macos)
            install_conda_macos
            ;;
        linux)
            install_conda_linux
            ;;
        windows)
            install_conda_windows
            ;;
        *)
            print_error "Unsupported operating system: $OSTYPE"
            print_info "Please install Miniconda manually from: https://docs.conda.io/en/latest/miniconda.html"
            script_exit 1
            ;;
    esac
}

# Get the project root directory (parent of scripts directory)
get_project_root() {
    local script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    echo "$(dirname "$script_dir")"
}

# Main execution
main() {
    print_info "Starting environment setup..."
    
    # Check if conda is installed
    if ! check_conda; then
        print_warn "Conda is not installed."
        
        # Check if we're in an interactive terminal
        if [[ -t 0 ]]; then
            read -p "Do you want to install Miniconda? (y/n) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                install_conda
                # After installation, we need to reload conda
                if ! check_conda; then
                    print_error "Conda installation completed but not found in PATH."
                    print_info "Please restart your terminal and run this script again."
                    script_exit 1
                fi
                # Initialize conda for the current shell after installation
                eval "$(conda shell.bash hook)"
            else
                print_error "Conda is required. Exiting."
                script_exit 1
            fi
        else
            print_error "Conda is required but not installed, and this is a non-interactive session."
            print_info "Please install Miniconda manually from: https://docs.conda.io/en/latest/miniconda.html"
            print_info "Or run this script in an interactive terminal to enable automatic installation."
            script_exit 1
        fi
    else
        print_info "Conda is already installed."
        # Initialize conda for the current shell before using conda commands
        eval "$(conda shell.bash hook)"
        conda --version
    fi
    
    # Get project root and environment.yml path
    local project_root=$(get_project_root)
    local env_file="${project_root}/environment.yml"
    
    if [[ ! -f "$env_file" ]]; then
        print_error "environment.yml not found at: $env_file"
        script_exit 1
    fi
    
    print_info "Creating conda environment from ${env_file}..."
    
    # Create or update the environment
    local env_name=$(grep '^name:' "$env_file" | awk '{print $2}')
    
    if conda env list | grep -q "^${env_name}"; then
        print_warn "Environment already exists. Updating..."
        conda env update -f "$env_file"
    else
        conda env create -f "$env_file"
    fi
    
    print_info "Environment setup complete!"
    print_info "To activate the environment, run: conda activate $env_name"
}

# Run main function
main "$@"
