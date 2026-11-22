FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY data/docs/ data/docs/
COPY scripts/ scripts/
RUN pip install --no-cache-dir . \
    && if [ ! -f data/company.db ]; then python scripts/make_data.py; fi \
    && useradd --create-home --uid 10001 kagent \
    && chown -R kagent:kagent /app
USER kagent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import json,os,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health',timeout=4); assert json.load(r)=={'status':'ok'}"
CMD ["sh", "-c", "exec uvicorn kagent.api.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
