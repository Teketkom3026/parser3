FROM node:20-alpine AS build

ARG VITE_BASE_PATH=/parser3
ENV VITE_BASE_PATH=${VITE_BASE_PATH}

WORKDIR /app
COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

FROM nginx:alpine
ARG VITE_BASE_PATH=/parser3
# Strip leading slash for the html directory name (e.g. /parser3 → parser3)
RUN echo "Base path: ${VITE_BASE_PATH}"
COPY --from=build /app/dist /usr/share/nginx/html${VITE_BASE_PATH}
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Inject base path into nginx config at runtime
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
EXPOSE 80
CMD ["/bin/sh", "-c", "envsubst '${VITE_BASE_PATH}' < /etc/nginx/conf.d/default.conf > /tmp/default.conf && cp /tmp/default.conf /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
