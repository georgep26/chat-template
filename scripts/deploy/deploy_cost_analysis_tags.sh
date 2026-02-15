#!/bin/bash

# Cost Allocation Tags Activation Script
# This script activates cost allocation tags in AWS Cost Explorer
# Activated tags can be used to filter and group costs in Cost Explorer
#
# Usage Examples:
#   # Activate all tags (Name, Environment, Project)
#   ./scripts/deploy/deploy_cost_analysis_tags.sh activate
#
#   # Activate specific tags
#   ./scripts/deploy/deploy_cost_analysis_tags.sh activate --tags Name,Environment
#
#   # List default cost allocation tags and their status
#   ./scripts/deploy/deploy_cost_analysis_tags.sh list
#
#   # Check status of specific tags
#   ./scripts/deploy/deploy_cost_analysis_tags.sh status --tags Name,Environment,Project
#
# Note: Cost allocation tags must be activated before they can be used in Cost Explorer.
#       It may take up to 24 hours for activated tags to appear in Cost Explorer.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
# SCRIPT_DIR used later for deploy_summary.sh

# Function to show usage
show_usage() {
    echo "Cost Allocation Tags Activation Script"
    echo ""
    echo "Usage: $0 [action] [options]"
    echo ""
  echo "Actions:"
  echo "  activate  - Activate cost allocation tags (default)"
  echo "  list      - List status of default cost allocation tags (Name, Environment, Project)"
  echo "  status    - Check status of specific tags"
    echo ""
    echo "Options:"
    echo "  --tags <tag1,tag2,...>  - Specific tags to activate/check (default: Name,Environment,Project)"
    echo "  --region <region>       - AWS region (default: us-east-1, but Cost Explorer is global)"
    echo "  -y, --yes               - Skip confirmation prompt (for activate action)"
    echo ""
    echo "Examples:"
    echo "  $0 activate"
    echo "  $0 activate --tags Name,Environment"
    echo "  $0 list"
    echo "  $0 status --tags Name,Environment,Project"
    echo ""
    echo "Note: Cost allocation tags must be activated before they can be used in Cost Explorer."
    echo "      It may take up to 24 hours for activated tags to appear in Cost Explorer."
    echo "      Cost Explorer is a global service, so region doesn't matter for tag activation."
}

# Default tags to activate
DEFAULT_TAGS=("Name" "Environment" "Project")
TAGS_TO_ACTIVATE=()
AWS_REGION="us-east-1"  # Cost Explorer is global, but we need a region for AWS CLI
ACTION="activate"
AUTO_CONFIRM=false

# Get the directory where the script is located
PROJECT_ROOT="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
cd "$PROJECT_ROOT"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        activate|list|status)
            ACTION="$1"
            shift
            ;;
        --tags)
            IFS=',' read -ra TAGS_TO_ACTIVATE <<< "$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# If no tags specified, use defaults
if [ ${#TAGS_TO_ACTIVATE[@]} -eq 0 ]; then
    TAGS_TO_ACTIVATE=("${DEFAULT_TAGS[@]}")
fi

print_header "Cost Allocation Tags Management"
print_info "Action: $ACTION"
print_info "Tags: ${TAGS_TO_ACTIVATE[*]}"
print_info "Region: $AWS_REGION (Cost Explorer is global)"

if [ "$ACTION" = "activate" ] && [ "$AUTO_CONFIRM" = false ]; then
    echo ""
    print_step "Summary: Activate cost allocation tags (${TAGS_TO_ACTIVATE[*]}) in the account."
    source "$SCRIPT_DIR/../utils/deploy_summary.sh"
    confirm_deployment "Proceed with activating cost allocation tags?" || exit 0
fi

# Function to check if AWS CLI is available
check_aws_cli() {
    if ! command -v aws &> /dev/null; then
        print_error "AWS CLI is not installed or not in PATH"
        print_error "Please install AWS CLI: https://aws.amazon.com/cli/"
        exit 1
    fi
    
    # Check if AWS credentials are configured
    if ! aws sts get-caller-identity --region "$AWS_REGION" >/dev/null 2>&1; then
        print_error "AWS credentials are not configured or invalid"
        print_error "Please configure AWS credentials using 'aws configure' or environment variables"
        exit 1
    fi
    
    print_info "AWS CLI is available and credentials are configured"
}

# Function to activate cost allocation tags
activate_tags() {
    print_step "Activating cost allocation tags..."
    
    local success_count=0
    local fail_count=0
    local already_active_count=0
    
    for tag in "${TAGS_TO_ACTIVATE[@]}"; do
        print_info "Activating tag: $tag"
        
        # Check if tag is already active
        local tag_status=$(aws ce list-cost-allocation-tags \
            --region us-east-1 \
            --query "CostAllocationTags[?TagKey=='$tag'].Status" \
            --output text 2>/dev/null || echo "NOT_FOUND")
        
        if [ "$tag_status" == "Active" ]; then
            print_warning "Tag '$tag' is already active"
            ((already_active_count++))
            continue
        fi
        
        # Activate the tag using update-cost-allocation-tags-status
        local tag_json="[{\"TagKey\": \"$tag\", \"Status\": \"Active\"}]"
        local update_output=$(aws ce update-cost-allocation-tags-status \
            --cost-allocation-tags-status "$tag_json" \
            --region us-east-1 2>&1)
        local update_result=$?
        
        if [ $update_result -eq 0 ]; then
            print_info "✓ Successfully activated tag: $tag"
            ((success_count++))
        else
            # Check if the error is because tag doesn't exist yet
            if echo "$update_output" | grep -qi "not found\|does not exist\|UnknownTagKeyException"; then
                print_warning "Tag '$tag' not found. It will be activated automatically when resources with this tag are created."
                print_warning "You may need to wait for resources to be tagged before this tag can be activated."
            else
                print_error "Failed to activate tag: $tag"
                echo "$update_output" | head -5
                ((fail_count++))
            fi
        fi
    done
    
    echo ""
    print_header "Activation Summary"
    print_info "Successfully activated: $success_count"
    print_info "Already active: $already_active_count"
    if [ $fail_count -gt 0 ]; then
        print_error "Failed: $fail_count"
    fi
    
    if [ $success_count -gt 0 ] || [ $already_active_count -gt 0 ]; then
        echo ""
        print_info "Note: It may take up to 24 hours for activated tags to appear in Cost Explorer."
        print_info "Once active, you can use these tags to filter and group costs in Cost Explorer."
    fi
}

# Function to list default cost allocation tags
list_tags() {
    print_step "Listing default cost allocation tags: ${DEFAULT_TAGS[*]}"
    
    local found_count=0
    local not_found_tags=()
    
    echo ""
    printf "%-20s %-15s\n" "Tag Key" "Status"
    printf "%-20s %-15s\n" "-------" "------"
    
    for tag in "${DEFAULT_TAGS[@]}"; do
        local tag_info=$(aws ce list-cost-allocation-tags \
            --region us-east-1 \
            --query "CostAllocationTags[?TagKey=='$tag'].[TagKey,Status]" \
            --output text 2>&1)
        
        if [ -n "$tag_info" ] && [ "$tag_info" != "None" ]; then
            local status=$(echo "$tag_info" | awk '{print $2}')
            ((found_count++))
            
            if [ "$status" == "Active" ]; then
                printf "%-20s ${GREEN}%-15s${NC}\n" "$tag" "$status"
            elif [ "$status" == "Inactive" ]; then
                printf "%-20s ${YELLOW}%-15s${NC}\n" "$tag" "$status"
            else
                printf "%-20s %-15s\n" "$tag" "$status"
            fi
        else
            not_found_tags+=("$tag")
            printf "%-20s ${RED}%-15s${NC}\n" "$tag" "Not Found"
        fi
    done
    
    echo ""
    print_header "Summary"
    print_info "Tags found: $found_count"
    if [ ${#not_found_tags[@]} -gt 0 ]; then
        print_warning "Tags not found: ${not_found_tags[*]}"
        print_warning "These tags will appear once resources are tagged with them"
    fi
}

# Function to check status of specific tags
check_status() {
    print_step "Checking status of tags: ${TAGS_TO_ACTIVATE[*]}"
    
    local found_count=0
    local not_found_tags=()
    
    for tag in "${TAGS_TO_ACTIVATE[@]}"; do
        local tag_info=$(aws ce list-cost-allocation-tags \
            --region us-east-1 \
            --query "CostAllocationTags[?TagKey=='$tag'].[TagKey,Status]" \
            --output text 2>&1)
        
        if [ -n "$tag_info" ] && [ "$tag_info" != "None" ]; then
            local status=$(echo "$tag_info" | awk '{print $2}')
            ((found_count++))
            
            if [ "$status" == "Active" ]; then
                print_info "✓ $tag: ${GREEN}Active${NC}"
            elif [ "$status" == "Inactive" ]; then
                print_warning "○ $tag: ${YELLOW}Inactive${NC} (needs activation)"
            else
                print_info "○ $tag: $status"
            fi
        else
            not_found_tags+=("$tag")
            print_warning "✗ $tag: Not found (tag may not exist yet or no resources are tagged with it)"
        fi
    done
    
    echo ""
    print_header "Status Summary"
    print_info "Tags found: $found_count"
    if [ ${#not_found_tags[@]} -gt 0 ]; then
        print_warning "Tags not found: ${not_found_tags[*]}"
        print_warning "These tags will appear once resources are tagged with them"
    fi
}

# Main execution
check_aws_cli

case $ACTION in
    activate)
        activate_tags
        ;;
    list)
        list_tags
        ;;
    status)
        check_status
        ;;
    *)
        print_error "Invalid action: $ACTION"
        show_usage
        exit 1
        ;;
esac

print_info "Cost allocation tags operation completed successfully"

