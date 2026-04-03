#!/bin/bash
export ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY /home/jeff/course-index/.env | cut -d= -f2)
export VOYAGE_API_KEY=$(grep VOYAGE_API_KEY /home/jeff/course-index/.env | cut -d= -f2)
cd /home/jeff/course-index
/home/jeff/course-index/.venv/bin/python3 /home/jeff/course-index/mcp_server.py
