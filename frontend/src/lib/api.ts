/**
 * Axios instance — JWT refresh interceptor wired in Day 1 afternoon block.
 */
import axios from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

export const api = axios.create({
  baseURL,
  withCredentials: false,
  timeout: 10_000,
});
