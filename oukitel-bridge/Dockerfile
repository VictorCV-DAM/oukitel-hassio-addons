ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Install Python3, pip, and required system build packages
RUN apk add --no-cache \
    python3 \
    py3-pip \
    jq

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

# Copy application scripts
COPY rootfs /
COPY oukitel_cloud_mqtt.py /app/

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
