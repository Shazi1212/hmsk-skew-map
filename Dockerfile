# Untested: Docker was not available where this was written.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Serve only the dashboard and its data, never the pipeline source.
RUN mkdir -p /srv/site data \
 && ln -s /app/index.html /srv/site/index.html \
 && ln -s /app/robots.txt /srv/site/robots.txt \
 && ln -s /app/letterpress.css /srv/site/letterpress.css \
 && ln -s /app/fonts /srv/site/fonts \
 && ln -s /app/data /srv/site/data
EXPOSE 8080
CMD ["sh", "/app/docker/run.sh"]
