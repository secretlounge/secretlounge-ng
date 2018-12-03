FROM node:alpine

WORKDIR /opt/secretlounge

COPY . .

RUN apk update && apk upgrade && \
  apk add --no-cache python3 bash git && \
  python3 -m ensurepip && \
  rm -r /usr/lib/python*/ensurepip && \
  pip3 install -r requirements.txt && \
  if [ ! -e /usr/bin/pip ]; then ln -s pip3 /usr/bin/pip ; fi && \
  if [[ ! -e /usr/bin/python ]]; then ln -sf /usr/bin/python3 /usr/bin/python; fi && \
  rm -r /root/.cache

CMD ["./secretlounge-ng"]
