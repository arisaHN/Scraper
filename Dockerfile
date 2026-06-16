FROM python:3.11-slim

WORKDIR /Scraper

COPY requirements.txt .
RUN apt-get update && apt-get install -y libpq-dev gcc \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && camoufox fetch \
    && playwright install-deps firefox \
    && sed -i \
       -e 's/pageError\.location\.url/pageError.location?.url ?? ""/g' \
       -e 's/pageError\.location\.lineNumber/pageError.location?.lineNumber ?? 0/g' \
       -e 's/pageError\.location\.columnNumber/pageError.location?.columnNumber ?? 0/g' \
       /usr/local/lib/python3.11/site-packages/playwright/driver/package/lib/coreBundle.js \
    && ( ! grep -E 'pageError\.location\.(url|lineNumber|columnNumber)\b' \
         /usr/local/lib/python3.11/site-packages/playwright/driver/package/lib/coreBundle.js \
         || ( echo "ERROR: coreBundle.js sed patch did not apply — playwright's bundled driver changed, fix the patterns above" >&2 && exit 1 ) ) \
    && rm -rf /var/lib/apt/lists/*
COPY . .

RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]