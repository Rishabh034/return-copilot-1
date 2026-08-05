#!/usr/bin/env bash
export PYTHONUNBUFFERED=1
export APP_ENVIRONMENT=PROD

cd /home/ubuntu/clickpost-ai-projects;
/home/ubuntu/ai-agent-env/bin/python3 app/agenticintegrations/scripts/carrier_api_testing_consumer.py



