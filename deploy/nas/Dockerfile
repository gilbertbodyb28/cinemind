# CineMind on the NAS, next to MediaManager. The frontend is built on the Mac
# (scripts/deploy_nas.sh) and copied in prebuilt; one uvicorn serves API + UI.
FROM python:3.11-slim
WORKDIR /app
COPY deploy/nas/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend/ /app/backend/
COPY frontend/build/ /app/frontend/build/
ENV PYTHONUNBUFFERED=1
WORKDIR /app/backend
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --retries=5 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8001/api/',timeout=4)"
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--loop", "asyncio", "--http", "h11"]
