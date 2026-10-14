#!/bin/bash
# Stop CR Automation Agentic server
PID=$(netstat -ano 2>/dev/null | grep ":8000.*LISTENING" | awk '{print $5}' | head -1)

if [ -z "$PID" ]; then
    echo "Server is not running on port 8000."
    exit 0
fi

echo "Stopping server (PID: $PID) ..."
taskkill //F //PID "$PID" 2>/dev/null || kill -9 "$PID" 2>/dev/null
echo "Server stopped."
