FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY roottrace ./roottrace
RUN pip install --no-cache-dir .
CMD ["python", "-m", "roottrace", "demo"]
