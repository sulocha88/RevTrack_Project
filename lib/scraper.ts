import { spawn } from "child_process";
import path from "path";
import { getPythonPath } from './python-utils';

export interface ProductData {
  title: string;
  price: string;
  original_price: string | null;
  discount_percentage: string | null;
  rating: number;
  reviewCount: number;
  availability: string;
  images: string[];
  description: string;
  features: string[];
  asin: string;
}

// Retry configuration - Optimized for speed
const MAX_RETRIES = 2; // Node-level retries for the Python process
const RETRY_DELAY = 1000; // Base delay (ms) for exponential backoff between attempts
const PROCESS_TIMEOUT_MS = 60000; // Allow up to 60s for Playwright-based scripts to finish

// Function to sleep for a given number of milliseconds
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Function to calculate exponential backoff delay
const getRetryDelay = (attempt: number) => RETRY_DELAY * Math.pow(2, attempt);

// Generic function to run a Python script with retry logic and rate limiting
const runPythonScript = async (scriptPath: string, arg: string, maxRetries: number = MAX_RETRIES): Promise<any> => {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await new Promise<any>((resolve, reject) => {
        console.log(`Running ${scriptPath}, attempt ${attempt + 1}/${maxRetries + 1}`);

        // Use an absolute path to prevent issues with the current working directory
        const absoluteScriptPath = path.join(process.cwd(), scriptPath);
        const pythonPath = getPythonPath();
        const pythonProcess = spawn(pythonPath, [absoluteScriptPath, arg]);
        let data = "";
        let errorData = "";
        let timeout: NodeJS.Timeout;

        // Set a timeout for the process.
        // This should be longer than the Playwright navigation/selector timeouts
        // so that Python can handle timeouts gracefully and return JSON instead of
        // being killed mid-run (which can cause EPIPE errors in Playwright's driver).
        timeout = setTimeout(() => {
          pythonProcess.kill();
          reject(new Error(`Process timed out after ${PROCESS_TIMEOUT_MS / 1000} seconds`));
        }, PROCESS_TIMEOUT_MS);

        pythonProcess.stdout.on("data", (chunk) => (data += chunk.toString()));
        pythonProcess.stderr.on("data", (chunk) => (errorData += chunk.toString()));

        pythonProcess.on("close", (code) => {
          clearTimeout(timeout);

          if (code !== 0) {
            // Combine both stdout and stderr for full error context
            const fullOutput = `stdout: ${data}\nstderr: ${errorData}`;
            const errorMessage = `Python script failed with code ${code}\n${fullOutput}`;
            console.error(`Script ${scriptPath} failed:`, errorMessage);
            console.error(`Python path used: ${pythonPath}`);
            console.error(`Script path: ${absoluteScriptPath}`);

            // Check if this is a rate limiting error
            if (errorData.includes('429') || errorData.includes('rate limit') ||
                errorData.includes('too many requests') || errorData.includes('blocked')) {
              reject(new Error('RATE_LIMITED'));
              return;
            }

            reject(new Error(errorMessage));
            return;
          }

          try {
            const parsed = JSON.parse(data);
            console.log(`${scriptPath} completed successfully`);
            resolve(parsed.data ?? parsed ?? {}); // fallback if JSON structure varies
          } catch (err) {
            console.error(`Failed to parse output. stdout: ${data}, stderr: ${errorData}`);
            reject(new Error("Failed to parse Python script output: " + err));
          }
        });

        pythonProcess.on("error", (err) => {
          clearTimeout(timeout);
          reject(new Error(`Failed to start Python process: ${err.message}`));
        });
      });
    } catch (error: any) {
      lastError = error;
      console.error(`Attempt ${attempt + 1} failed:`, error.message);

      // If this is the last attempt, don't retry
      if (attempt === maxRetries) {
        break;
      }

      // Check if error is rate limiting related
      if (error.message === 'RATE_LIMITED') {
        const delay = getRetryDelay(attempt);
        console.log(`Rate limited detected, waiting ${delay}ms before retry...`);
        await sleep(delay);
        continue;
      }

      // For other errors, wait before retrying
      const delay = getRetryDelay(attempt);
      console.log(`Retrying ${scriptPath} in ${delay}ms...`);
      await sleep(delay);
    }
  }

  // If we get here, all retries failed
  throw lastError || new Error(`All retry attempts failed for ${scriptPath}`);
};

// Scrape product data
export const scrapeAmazonProduct = async (url: string): Promise<ProductData> => {
  const data = await runPythonScript("scripts/product.py", url);
  return {
    asin: data.asin ?? "N/A",
    title: data.title ?? "N/A",
    price: data.price ?? "N/A",
    original_price: data.original_price ?? null,
    discount_percentage: data.discount_percentage ?? null,
    rating: Number(data.rating ?? 0),
    reviewCount: Number(data.reviewCount ?? 0),
    availability: data.availability ?? "N/A",
    features: Array.isArray(data.features) ? data.features : [],
    description: data.description ?? "N/A",
    images: Array.isArray(data.images) ? data.images : []
  };
};

// Scrape reviews
export const analyzeReviews = async (url: string): Promise<any[]> => {
  const data = await runPythonScript("scripts/script2.py", url);
  if (!Array.isArray(data)) return [];
  return data.map((r: any) => ({
    Description: r.Description ?? "",
    Stars: r.Stars ?? "0"
  }));
};

// Generate FAKE price history for demonstration purposes.
// In a real application, this data should be stored and retrieved from a database
// over time, not fabricated on each request.
export const generatePriceHistory = (currentPrice: number) => {
  const history: Array<{ date: string; price: number }> = [];
  const today = new Date();
  for (let i = 3; i >= 0; i--) {
    const date = new Date(today);
    date.setMonth(today.getMonth() - i);
    const variation = (Math.random() - 0.5) * 10;
    const price = Math.max(currentPrice + variation, currentPrice * 0.8);
    history.push({ date: date.toISOString().split("T")[0], price: Math.round(price * 100) / 100 });
  }
  return history;
};
