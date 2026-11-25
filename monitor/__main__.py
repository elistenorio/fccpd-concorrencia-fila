"""python -m monitor  ->  painel em http://localhost:8002"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("monitor.servidor:app", host="0.0.0.0", port=int(os.getenv("MONITOR_PORTA", "8002")))
