# Plan B for hosting (Render, Railway, Hugging Face Spaces with the Docker SDK, Fly.io).
# Not needed for Streamlit Community Cloud or for running locally.
# NOTE: written carefully but not built by the author's tooling. Build it once before relying on it:
#   docker build -t invoice-desk .
#   docker run -p 8501:8501 invoice-desk
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# the SQLite file and stored uploads live outside the image layer
ENV INVOICE_DESK_HOME=/tmp/invoice_desk
EXPOSE 8501
CMD ["sh", "-c", "streamlit run app.py --server.port ${PORT:-8501} --server.address 0.0.0.0"]
