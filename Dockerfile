# Plan B for hosting (Render, Railway, Hugging Face Spaces with the Docker SDK, Fly.io).
# Not needed for Streamlit Community Cloud or for running locally.
#
# NOT BUILT in the environment this project was made in: that machine could not reach an image registry, so
# `FROM python:3.11-slim` could not be pulled. Build it once before relying on it:
#   docker build -t invoice-desk .
#   docker run --rm -p 8501:8501 invoice-desk          then open http://localhost:8501
#   docker run --rm invoice-desk python -m pytest      (the whole test suite inside the image)
# For a public link add:  -e INVOICE_DESK_SANDBOX=session   (every visitor gets private books)
# For live Claude calls:  -e ANTHROPIC_API_KEY=... -e LLM_ALLOW_LIVE=1   (the daily cap still applies)
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# the SQLite file and stored uploads live outside the image layer; recorded answers only unless told otherwise
ENV INVOICE_DESK_HOME=/tmp/invoice_desk \
    LLM_MODE=fixture \
    INVOICE_DESK_TODAY=2026-11-21
EXPOSE 8501
CMD ["sh", "-c", "streamlit run app.py --server.port ${PORT:-8501} --server.address 0.0.0.0"]
