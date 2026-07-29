FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173

# Vite's own dev server is fine for a portfolio/demo deployment -- for a
# real production build you'd instead `npm run build` and serve dist/
# with a static file server (nginx, etc).
CMD ["npm", "run", "dev", "--", "--host"]
