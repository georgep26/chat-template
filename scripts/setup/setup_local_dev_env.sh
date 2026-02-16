#!/bin/bash
# Local dev environment setup: conda env from environment.yml, PYTHONPATH to project root
#
# Usage:
#   ./scripts/setup/setup_local_dev_env.sh
#   ./scripts/setup/setup_local_dev_env.sh -y   # Skip confirmation

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

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
        print_warning "Homebrew not found. Installing Miniconda via direct download..."
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

# Check if Chocolatey is available (Windows)
check_chocolatey() {
    if command -v choco &> /dev/null; then
        return 0
    fi
    # Chocolatey may be on PATH only in PowerShell; try running it
    if choco --version &> /dev/null; then
        return 0
    fi
    return 1
}

# Install conda on Windows (Git Bash/Cygwin) via Chocolatey when available
install_conda_windows() {
    if check_chocolatey; then
        print_info "Installing Miniconda on Windows via Chocolatey..."
        choco install miniconda3 -y
        # Chocolatey adds conda to PATH for new sessions; common install locations for current session
        local conda_paths=(
            "/c/ProgramData/miniconda3/Scripts"
            "/c/ProgramData/miniconda3"
            "/c/tools/miniconda3/Scripts"
            "/c/tools/miniconda3"
            "$HOME/miniconda3/Scripts"
            "$HOME/miniconda3"
        )
        for p in "${conda_paths[@]}"; do
            if [[ -x "${p}/conda.exe" || -x "${p}/conda" ]]; then
                export PATH="${p}:$(dirname "$p"):$PATH"
                break
            fi
        done
        if ! check_conda; then
            print_warning "Miniconda was installed but conda is not in PATH in this session."
            print_info "Please restart your terminal and run this script again."
            exit 1
        fi
        print_info "Miniconda installed via Chocolatey. Conda is available in this session."
    else
        print_error "Windows installation via script requires Chocolatey (https://chocolatey.org/)."
        print_info "Install Chocolatey, then run this script again. Or install Miniconda manually from: https://docs.conda.io/en/latest/miniconda.html"
        print_info "After installation, restart your terminal and run this script again."
        exit 1
    fi
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
            exit 1
            ;;
    esac
}

# Get the project root directory (parent of scripts directory)
get_project_root() {
    local script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    echo "$(dirname "$(dirname "$script_dir")")"
}

# Main execution
main() {
    local auto_confirm=false
    while [[ $# -gt 0 ]]; do
        case $1 in
            -y|--yes)
                auto_confirm=true
                shift
                ;;
            -h|--help)
                echo "Local Dev Environment Setup"
                echo ""
                echo "Usage: $0 [options]"
                echo ""
                echo "Options:"
                echo "  -y, --yes   Skip confirmation prompt"
                echo "  -h, --help  Show this help"
                exit 0
                ;;
            *)
                shift
                ;;
        esac
    done

    print_header "Local Dev Environment Setup"

    # Resolve paths early for summary
    local project_root=$(get_project_root)
    local env_file="${project_root}/environment.yml"
    local env_name=""
    if [[ -f "$env_file" ]]; then
        env_name=$(grep '^name:' "$env_file" | awk '{print $2}')
    fi

    # Setup summary
    echo ""
    print_info "Setup configuration:"
    print_info "  Project root: $project_root"
    print_info "  Environment file: $env_file"
    if [[ -n "$env_name" ]]; then
        print_info "  Conda env name: $env_name"
    fi
    echo ""
    print_info "This script will:"
    print_info "  1. Check for conda (install Miniconda if missing and you confirm)"
    print_info "  2. Create or update conda environment from environment.yml"
    print_info "  3. Set PYTHONPATH to project root when the env is activated"
    echo ""

    if [[ "$auto_confirm" = false ]] && [[ -t 0 ]]; then
        confirm_deployment "Proceed with local dev environment setup?" || exit 0
    fi

    print_info "Starting environment setup..."
    
    # Check if conda is installed
    if ! check_conda; then
        print_warning "Conda is not installed."
        
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
                    exit 1
                fi
                # Initialize conda for the current shell after installation
                eval "$(conda shell.bash hook)"
            else
                print_error "Conda is required. Exiting."
                exit 1
            fi
        else
            print_error "Conda is required but not installed, and this is a non-interactive session."
            print_info "Please install Miniconda manually from: https://docs.conda.io/en/latest/miniconda.html"
            print_info "Or run this script in an interactive terminal to enable automatic installation."
            exit 1
        fi
    else
        print_info "Conda is already installed."
        # Initialize conda for the current shell before using conda commands
        eval "$(conda shell.bash hook)"
        conda --version
    fi
    
    if [[ ! -f "$env_file" ]]; then
        print_error "environment.yml not found at: $env_file"
        exit 1
    fi
    
    print_info "Creating conda environment from ${env_file}..."
    
    # Create or update the environment (env_name already set from summary)
    [[ -z "$env_name" ]] && env_name=$(grep '^name:' "$env_file" | awk '{print $2}')
    
    if conda env list | grep -q "^${env_name}"; then
        print_warning "Environment already exists. Updating..."
        conda env update -f "$env_file"
    else
        conda env create -f "$env_file"
    fi
    
    # Create activation script to set PYTHONPATH to project root
    local env_path=$(conda env list | grep "^${env_name}" | awk '{print $NF}')
    if [[ -n "$env_path" && -d "$env_path" ]]; then
        local activate_dir="${env_path}/etc/conda/activate.d"
        mkdir -p "$activate_dir"
        
        local activate_script="${activate_dir}/set_pythonpath.sh"
        cat > "$activate_script" << EOF
#!/bin/bash
# Set PYTHONPATH to project root when conda environment is activated
export PYTHONPATH="${project_root}:\${PYTHONPATH}"
EOF
        chmod +x "$activate_script"
        print_info "Created activation script to set PYTHONPATH to project root"
    else
        print_warning "Could not determine environment path. PYTHONPATH will need to be set manually."
    fi
    
    # Completion summary
    echo ""
    echo ""
    draw_box_top 64
    draw_box_row_centered "LOCAL DEV ENVIRONMENT SETUP COMPLETE" 64
    draw_box_separator 64
    draw_box_row "Environment: $env_name" 64
    draw_box_row "Project root: $project_root" 64
    draw_box_separator 64
    draw_box_row "To activate: conda activate $env_name" 64
    draw_box_bottom 64
    echo ""
    print_complete "Environment setup complete!"
    print_info "To activate the environment, run: conda activate $env_name"
}

# Run main function
main "$@"
