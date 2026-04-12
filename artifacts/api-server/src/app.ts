import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import { createProxyMiddleware } from "http-proxy-middleware";
import router from "./routes";
import { logger } from "./lib/logger";

const STREAMLIT_PORT = process.env["STREAMLIT_PORT"] || "8501";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());

app.use("/api", express.json(), express.urlencoded({ extended: true }), router);

app.use(
  "/",
  createProxyMiddleware({
    target: `http://127.0.0.1:${STREAMLIT_PORT}`,
    changeOrigin: true,
    ws: true,
  }),
);

export default app;
