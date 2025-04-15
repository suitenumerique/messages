FROM debian:12.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
	ca-certificates \
  postfix \
  python3-pip \
  python3-venv \
  curl \
  procps \
  mailutils \
  && rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONIOENCODING=utf-8

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY ./scripts /app/scripts
COPY ./etc /etc/postfix/
COPY ./entrypoint.sh /entrypoint.sh

RUN chmod +x /app/scripts/*.py /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]