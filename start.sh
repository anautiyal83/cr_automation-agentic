#!/bin/bash
# Start CR Automation Agentic server
cd "$(dirname "$0")"

# Activate venv
if [ -d ".venv" ]; then
    source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null
fi

# Ensure data directory exists
mkdir -p data/uploads data/runs data/logs data/knowledge

# Kill existing instance if running
EXISTING_PID=$(netstat -ano 2>/dev/null | grep ":8000.*LISTENING" | awk '{print $5}' | head -1)
if [ -n "$EXISTING_PID" ]; then
    echo "Stopping existing server (PID: $EXISTING_PID) ..."
    taskkill //F //PID "$EXISTING_PID" 2>/dev/null || kill -9 "$EXISTING_PID" 2>/dev/null
    sleep 1
fi

LOG_FILE="data/logs/server_$(date +%Y%m%d_%H%M%S).log"

echo "============================================"
echo "  CR Automation Agentic Pipeline Server"
echo "============================================"
echo "  URL:  http://localhost:8000"
echo "  Log:  $LOG_FILE"
echo "  Stop: ./stop.sh"
echo "============================================"
echo ""

# Run with logs to both console and file
python -u src/ui/app.py 2>&1 | tee "$LOG_FILE"
