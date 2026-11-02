FROM python:3.12-slim

RUN pip install --no-cache-dir garmin-fit-sdk==21.218.0 tzdata \
    && useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin report \
    && install -d -o report -g report /state

COPY report.py /app/report.py

USER report
ENTRYPOINT ["python", "-u", "/app/report.py"]
