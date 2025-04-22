FROM node:18-alpine AS build

WORKDIR /app

COPY ./BillNote_frontend /app

RUN npm install && npm run build

FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
