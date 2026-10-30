FROM python:3.13-slim

RUN useradd --create-home --uid 1000 cloudobsai \
    && install -d -o cloudobsai /var/lib/cloudobsai/home /var/lib/cloudobsai/cwd /var/lib/cloudobsai/data

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
COPY prompts ./prompts
RUN pip install --no-cache-dir .

# The agent runs from an empty directory outside the code (SPEC 7.1).
ENV CLOUDOBSAI_STATE_DIR=/var/lib/cloudobsai/home \
    CLOUDOBSAI_WORK_DIR=/var/lib/cloudobsai/cwd

USER cloudobsai
CMD ["python", "-m", "cloudobsai.main"]
