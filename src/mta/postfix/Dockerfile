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

COPY . /app
COPY ./etc /etc/postfix/

RUN pip install -e .

RUN chmod +x *.py entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]