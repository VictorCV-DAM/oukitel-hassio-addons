ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Install build dependencies if needed for pycryptodome on some arches
RUN apk add --no-cache \
    python3 \
    py3-pip \
    jq \
    gcc \
    musl-dev \
    libffi-dev

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

# Copy application scripts
COPY rootfs /
COPY oukitel_cloud_mqtt.py /app/

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
