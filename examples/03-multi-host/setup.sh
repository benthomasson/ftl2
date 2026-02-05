#!/bin/bash
# FTL2 Multi-Host Example - Setup Script
# Manages the Docker container lifecycle for 3 SSH servers

set -e

COMPOSE_FILE="docker-compose.yml"
CONTAINERS=("ftl2-example-web01" "ftl2-example-web02" "ftl2-example-db01")

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if Docker is available
check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}Error: docker command not found${NC}"
        echo "Please install Docker or start Colima"
        echo
        echo "For macOS with Colima:"
        echo "  brew install colima docker"
        echo "  colima start"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        echo -e "${RED}Error: Docker daemon not running${NC}"
        echo
        echo "For macOS with Colima:"
        echo "  colima start"
        exit 1
    fi
}

# Set up SSH key authentication for all containers
setup_ssh_keys() {
    local ssh_key="$HOME/.ssh/ftl2_multihost_rsa"

    # Generate SSH key if it doesn't exist
    if [ ! -f "$ssh_key" ]; then
        echo "  Generating SSH key..."
        ssh-keygen -t rsa -b 4096 -f "$ssh_key" -N "" -C "ftl2-multihost" > /dev/null 2>&1
    fi

    # Copy public key to all containers
    echo "  Copying SSH key to containers..."
    for container in "${CONTAINERS[@]}"; do
        docker exec "$container" mkdir -p /config/.ssh 2>/dev/null
        cat "${ssh_key}.pub" | docker exec -i "$container" tee /config/.ssh/authorized_keys > /dev/null
        docker exec "$container" chmod 700 /config/.ssh
        docker exec "$container" chmod 600 /config/.ssh/authorized_keys
        docker exec "$container" chown -R 1000:1000 /config/.ssh
    done

    echo -e "${GREEN}  SSH key configured: $ssh_key${NC}"
}

# Start all containers
start_containers() {
    echo -e "${GREEN}Starting multi-host SSH environment...${NC}"
    echo -e "${BLUE}Containers: web01, web02, db01${NC}"
    echo
    docker compose -f "$COMPOSE_FILE" up -d

    echo -e "${YELLOW}Waiting for containers to be ready...${NC}"

    # Wait for health checks
    local ready=0
    for i in {1..30}; do
        local healthy=0
        for container in "${CONTAINERS[@]}"; do
            if docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null | grep -q "healthy"; then
                ((healthy++))
            fi
        done

        if [ "$healthy" -eq "${#CONTAINERS[@]}" ]; then
            ready=1
            break
        fi

        echo -n "."
        sleep 1
    done

    echo

    if [ "$ready" -eq 1 ]; then
        echo -e "${GREEN}All containers are ready!${NC}"
    else
        echo -e "${YELLOW}Warning: Health check timeout, but containers may still work${NC}"
    fi

    # Install Python in all containers
    echo
    echo -e "${YELLOW}Installing Python in containers...${NC}"
    for container in "${CONTAINERS[@]}"; do
        docker exec "$container" apk add python3 > /dev/null 2>&1
    done
    echo -e "${GREEN}Python installed in all containers${NC}"

    # Set up SSH key authentication
    echo -e "${YELLOW}Setting up SSH key authentication...${NC}"
    setup_ssh_keys

    echo
    show_info
}

# Stop all containers
stop_containers() {
    echo -e "${YELLOW}Stopping all SSH server containers...${NC}"
    docker compose -f "$COMPOSE_FILE" down
    echo -e "${GREEN}All containers stopped${NC}"
}

# Show container status
status_containers() {
    echo -e "${GREEN}Container Status:${NC}"
    docker compose -f "$COMPOSE_FILE" ps
    echo

    # Show individual container health
    echo -e "${GREEN}Health Status:${NC}"
    for container in "${CONTAINERS[@]}"; do
        local health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "not running")
        local color=$RED
        if [ "$health" = "healthy" ]; then
            color=$GREEN
        fi
        printf "  ${color}%-25s %s${NC}\n" "$container" "$health"
    done
    echo

    if docker compose -f "$COMPOSE_FILE" ps | grep -q "Up"; then
        show_info
    fi
}

# Show connection information
show_info() {
    echo -e "${GREEN}SSH Connection Information:${NC}"
    echo
    echo "  web01:"
    echo "    ssh -p 2222 -i ~/.ssh/ftl2_multihost_rsa testuser@localhost"
    echo "    Host: 127.0.0.1:2222"
    echo
    echo "  web02:"
    echo "    ssh -p 2223 -i ~/.ssh/ftl2_multihost_rsa testuser@localhost"
    echo "    Host: 127.0.0.1:2223"
    echo
    echo "  db01:"
    echo "    ssh -p 2224 -i ~/.ssh/ftl2_multihost_rsa testuser@localhost"
    echo "    Host: 127.0.0.1:2224"
    echo
    echo "  Auth: SSH key (~/.ssh/ftl2_multihost_rsa)"
    echo

    echo -e "${GREEN}FTL2 Commands:${NC}"
    echo "  # Test all hosts"
    echo "  ftl2 -m ping -i inventory.yml"
    echo
    echo "  # Test webservers only"
    echo "  ftl2 -m ping -i inventory.yml --limit webservers"
    echo
    echo "  # Test databases only"
    echo "  ftl2 -m ping -i inventory.yml --limit databases"
    echo
    echo "  # Run examples"
    echo "  ./run_examples.sh"
    echo
}

# Show logs from all containers
logs_containers() {
    echo -e "${GREEN}Container Logs:${NC}"
    docker compose -f "$COMPOSE_FILE" logs -f
}

# Show logs from specific container
logs_container() {
    local service=$1
    if [ -z "$service" ]; then
        echo -e "${RED}Error: Please specify a service (web01, web02, or db01)${NC}"
        echo "Example: $0 logs-one web01"
        exit 1
    fi

    echo -e "${GREEN}Logs for $service:${NC}"
    docker compose -f "$COMPOSE_FILE" logs -f "$service"
}

# Restart all containers
restart_containers() {
    stop_containers
    echo
    start_containers
}

# Test SSH connectivity to all hosts
test_connections() {
    echo -e "${GREEN}Testing SSH connectivity...${NC}"
    echo

    local ports=(2222 2223 2224)
    local names=("web01" "web02" "db01")

    for i in "${!ports[@]}"; do
        local port="${ports[$i]}"
        local name="${names[$i]}"

        echo -n "Testing ${name} (port ${port})... "

        if nc -z localhost "$port" 2>/dev/null; then
            echo -e "${GREEN}OK${NC}"
        else
            echo -e "${RED}FAILED${NC}"
        fi
    done

    echo
    echo -e "${BLUE}Note: This only tests if ports are open, not SSH authentication${NC}"
    echo "To test full SSH login, run:"
    echo "  ssh -p 2222 testuser@localhost"
}

# Show usage
usage() {
    echo "FTL2 Multi-Host Example - Setup Script"
    echo
    echo "Usage: $0 {start|stop|restart|status|logs|logs-one|test|help}"
    echo
    echo "Commands:"
    echo "  start      - Start all SSH server containers"
    echo "  stop       - Stop and remove all containers"
    echo "  restart    - Restart all containers"
    echo "  status     - Show container status and health"
    echo "  logs       - Show logs from all containers (follow mode)"
    echo "  logs-one   - Show logs from specific container (usage: $0 logs-one web01)"
    echo "  test       - Test SSH connectivity to all hosts"
    echo "  help       - Show this help message"
    echo
    echo "Examples:"
    echo "  $0 start          # Start all containers"
    echo "  $0 status         # Check status"
    echo "  $0 test           # Test SSH connectivity"
    echo "  $0 logs-one web01 # View logs for web01"
    echo "  $0 stop           # Stop all containers"
}

# Main command handler
main() {
    case "$1" in
        start)
            check_docker
            start_containers
            ;;
        stop)
            check_docker
            stop_containers
            ;;
        restart)
            check_docker
            restart_containers
            ;;
        status)
            check_docker
            status_containers
            ;;
        logs)
            check_docker
            logs_containers
            ;;
        logs-one)
            check_docker
            logs_container "$2"
            ;;
        test)
            check_docker
            test_connections
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            echo -e "${RED}Error: Unknown command '$1'${NC}"
            echo
            usage
            exit 1
            ;;
    esac
}

# Run main with all arguments
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: No command specified${NC}"
    echo
    usage
    exit 1
fi

main "$@"
