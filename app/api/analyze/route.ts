import { NextResponse } from 'next/server';
import { scrapeAmazonProduct, analyzeReviews, generatePriceHistory } from '@/lib/scraper';
import { productCache } from '@/lib/cache';

export async function POST(request: Request) {
  try {
    const { url } = await request.json();

    if (!url || typeof url !== 'string') {
      return NextResponse.json({ error: 'Valid URL is required' }, { status: 400 });
    }

    // Basic URL validation
    try {
      new URL(url);
    } catch {
      return NextResponse.json({ error: 'Invalid URL format' }, { status: 400 });
    }

    const startTime = Date.now();

    // Validate Amazon URL
    const isValidAmazonUrl = (url: string): boolean => {
      try {
        const urlObj = new URL(url);
        const hostname = urlObj.hostname.toLowerCase();
        return hostname.includes("amazon") || hostname.includes("amzn");
      } catch {
        return false;
      }
    };

    if (!isValidAmazonUrl(url)) {
      return NextResponse.json({ error: 'Only Amazon URLs are supported' }, { status: 400 });
    }

    console.log("Starting comprehensive product analysis for:", url);
    
    // Check cache first
    const cachedResult = productCache.get(url);
    if (cachedResult) {
      console.log("Returning cached result");
      return NextResponse.json({
        ...cachedResult,
        cached: true,
        lastUpdated: new Date().toISOString()
      });
    }

    try {
      // Run product scraping and review analysis in parallel for better performance
      console.log("Starting parallel scraping...");
      
      const reviewsUrl = url.replace(/\/dp\//, '/product-reviews/').replace(/\?.*$/, '') + '/ref=cm_cr_dp_d_show_all_btm?pageNumber=1&sortBy=recent';
      
      const [productData, reviewsData] = await Promise.allSettled([
        scrapeAmazonProduct(url),
        analyzeReviews(reviewsUrl)
      ]);
      
      // Handle product data
      if (productData.status === 'rejected' || !productData.value || !productData.value.title || productData.value.title === "N/A") {
        console.error('Product scraping failed:', productData.status === 'rejected' ? productData.reason : 'No product data');
        return NextResponse.json({ 
          error: 'Unable to extract product information',
          details: 'The product page may be protected or temporarily unavailable. Please try again in a few minutes.'
        }, { status: 400 });
      }
      
      // Handle reviews data (non-critical, can fail)
      let reviews = [];
      if (reviewsData.status === 'fulfilled' && Array.isArray(reviewsData.value)) {
        reviews = reviewsData.value;
        console.log(`Successfully scraped ${reviews.length} reviews`);
      } else {
        console.warn('Review scraping failed or returned no data, continuing without reviews');
      }
      
      const productInfo = productData.value;

      // Parse current price for Indian currency
      const parseIndianPrice = (priceString: string): number => {
        if (!priceString || priceString === "N/A") return 1000; // Default fallback
        
        // Remove currency symbols and commas, handle both ₹ and $ symbols
        const cleanPrice = priceString.replace(/[₹$,\s]/g, '');
        const numericPrice = parseFloat(cleanPrice.replace(/[^0-9.]/g, ''));
        
        return !isNaN(numericPrice) && numericPrice > 0 ? numericPrice : 1000;
      };
      
      const currentPrice = parseIndianPrice(productInfo.price);
      const priceHistory = generatePriceHistory(currentPrice);

      // Calculate sentiment analysis from reviews OR product rating
      let sentimentAnalysis = {
        positive: 60,
        neutral: 25,
        negative: 15
      };

      if (reviews.length > 0) {
        // Simple sentiment analysis based on actual review ratings
        const ratings = reviews.map((review: any) => {
          const rating = parseFloat(review.Stars?.replace(/[^0-9.]/g, '') || '3');
          return rating;
        });

        if (ratings.length > 0) {
          const positive = ratings.filter(r => r >= 4).length;
          const negative = ratings.filter(r => r <= 2).length;
          const neutral = ratings.length - positive - negative;

          sentimentAnalysis = {
            positive: Math.round((positive / ratings.length) * 100),
            neutral: Math.round((neutral / ratings.length) * 100),
            negative: Math.round((negative / ratings.length) * 100)
          };
        }
      } else if (productInfo.rating) {
        // Fallback: Use product's overall rating to estimate sentiment
        const avgRating = parseFloat(String(productInfo.rating).replace(/[^0-9.]/g, '') || '0');
        
        if (avgRating >= 4.0) {
          // High rating: mostly positive
          sentimentAnalysis = {
            positive: Math.round(70 + (avgRating - 4) * 15),
            neutral: Math.round(20 - (avgRating - 4) * 5),
            negative: Math.round(10 - (avgRating - 4) * 5)
          };
        } else if (avgRating >= 3.0) {
          // Medium rating: mixed sentiment
          sentimentAnalysis = {
            positive: Math.round(40 + (avgRating - 3) * 30),
            neutral: Math.round(35 - (avgRating - 3) * 5),
            negative: Math.round(25 - (avgRating - 3) * 10)
          };
        } else if (avgRating > 0) {
          // Low rating: mostly negative
          sentimentAnalysis = {
            positive: Math.round(15 + avgRating * 8),
            neutral: Math.round(20 + avgRating * 5),
            negative: Math.round(65 - avgRating * 13)
          };
        }
      }

      // Extract key insights from reviews and product data
      const keyInsights: string[] = [];
      
      // Add rating-based insight
      const avgRating = parseFloat(String(productInfo.rating || '0').replace(/[^0-9.]/g, '') || '0');
      if (avgRating >= 4.5) {
        keyInsights.push("Highly rated product with excellent customer satisfaction");
      } else if (avgRating >= 4.0) {
        keyInsights.push("Well-received product with strong customer ratings");
      } else if (avgRating >= 3.0) {
        keyInsights.push("Product has mixed reviews from customers");
      } else if (avgRating > 0) {
        keyInsights.push("Product has lower ratings, consider alternatives");
      }

      // Add availability insight
      if (productInfo.availability && productInfo.availability !== 'N/A') {
        if (productInfo.availability.toLowerCase().includes('in stock')) {
          keyInsights.push("Currently available for immediate purchase");
        } else if (productInfo.availability.toLowerCase().includes('out of stock')) {
          keyInsights.push("Currently out of stock - check back later");
        }
      }

      // Add price insight
      if (productInfo.discount_percentage) {
        const discount = parseInt(productInfo.discount_percentage);
        if (discount >= 30) {
          keyInsights.push(`Great deal with ${discount}% discount currently available`);
        } else if (discount >= 15) {
          keyInsights.push(`Moderate discount of ${discount}% off regular price`);
        }
      }

      // Add review count insight
      if (reviews.length > 0) {
        keyInsights.push(`Analysis based on ${reviews.length} recent customer reviews`);
      } else if (productInfo.reviewCount && productInfo.reviewCount > 0) {
        keyInsights.push(`Product has ${productInfo.reviewCount} total customer reviews`);
      }
      
      // Fallback if no insights generated
      if (keyInsights.length === 0) {
        keyInsights.push("Limited review data available for detailed analysis");
      }

      // Generate pros and cons based on actual product data
      const pros: string[] = [];
      const cons: string[] = [];
      
      // Analyze pros
      if (avgRating >= 4.0) {
        pros.push(`High customer rating of ${productInfo.rating}/5.0`);
      }
      
      if (productInfo.discount_percentage && parseInt(productInfo.discount_percentage) >= 15) {
        pros.push(`Currently ${productInfo.discount_percentage}% off regular price`);
      }
      
      if (productInfo.availability?.toLowerCase().includes('in stock')) {
        pros.push("Available for immediate purchase");
      }
      
      if (productInfo.reviewCount && productInfo.reviewCount > 0) {
        const reviewCountNum = productInfo.reviewCount;
        if (!isNaN(reviewCountNum) && reviewCountNum > 100) {
          pros.push(`Trusted by ${productInfo.reviewCount} customers`);
        }
      }
      
      if (reviews.length > 0) {
        const posReviews = reviews.filter((r: any) => {
          const stars = parseFloat(r.Stars?.replace(/[^0-9.]/g, '') || '0');
          return stars >= 4;
        });
        if (posReviews.length / reviews.length >= 0.7) {
          pros.push("Majority of recent reviews are positive");
        }
      }
      
      // Analyze cons
      if (avgRating < 4.0 && avgRating > 0) {
        cons.push("Mixed or lower customer ratings");
      }
      
      if (!productInfo.discount_percentage || parseInt(productInfo.discount_percentage) < 5) {
        cons.push("Limited or no discount currently available");
      }
      
      if (productInfo.availability?.toLowerCase().includes('out of stock')) {
        cons.push("Currently out of stock");
      }
      
      if (reviews.length > 0) {
        const negReviews = reviews.filter((r: any) => {
          const stars = parseFloat(r.Stars?.replace(/[^0-9.]/g, '') || '0');
          return stars <= 2;
        });
        if (negReviews.length / reviews.length >= 0.2) {
          cons.push("Some customers report issues or dissatisfaction");
        }
      }
      
      // Add defaults if lists are empty
      if (pros.length === 0) {
        pros.push("Product available on Amazon marketplace");
        if (productInfo.features && productInfo.features.length > 0) {
          pros.push("Detailed product specifications available");
        }
      }
      
      if (cons.length === 0) {
        cons.push("Limited recent review data for comprehensive analysis");
      }

      // Price comparison with historical data
      const priceComparison = {
        current: productInfo.price,
        lowest: Math.min(...priceHistory.map(p => p.price)),
        highest: Math.max(...priceHistory.map(p => p.price)),
        average: priceHistory.reduce((sum, p) => sum + p.price, 0) / priceHistory.length,
        trend: priceHistory[priceHistory.length - 1].price > priceHistory[0].price ? 'increasing' : 'decreasing'
      };

      const result = {
        title: productInfo.title,
        productUrl: url,
        asin: productInfo.asin,
        price: {
          current: productInfo.price,
          original: productInfo.original_price,
          discount: productInfo.discount_percentage ? `${productInfo.discount_percentage}%` : null,
          comparison: priceComparison,
          history: priceHistory
        },
        rating: {
          average: productInfo.rating,
          count: productInfo.reviewCount
        },
        availability: productInfo.availability,
        images: productInfo.images,
        features: productInfo.features,
        description: productInfo.description,
        reviews: reviews.slice(0, 25), // Reduced from 50 to 25 for faster response
        analysis: {
          sentiment: sentimentAnalysis,
          keyInsights,
          pros,
          cons,
          reviewCount: reviews.length
        },
        lastUpdated: new Date().toISOString(),
        processingTime: Date.now() - startTime
      };

      console.log("Product analysis completed successfully");
      
      // Cache the result for future requests
      productCache.set(url, result);
      
      return NextResponse.json(result);

    } catch (error: any) {
      console.error("Analysis failed:", error.message);
      
      if (error.message.includes('rate limit') || error.message.includes('429')) {
        return NextResponse.json({
          error: 'Rate limit exceeded',
          details: 'Please wait a few minutes before analyzing another product'
        }, { status: 429 });
      }

      if (error.message.includes('timeout')) {
        return NextResponse.json({
          error: 'Analysis timeout',
          details: 'The analysis is taking too long. Please try again.'
        }, { status: 408 });
      }

      return NextResponse.json({
        error: 'Analysis failed',
        details: error.message
      }, { status: 500 });
    }

  } catch (error: any) {
    console.error("Request processing error:", error.message);
    return NextResponse.json({ 
      error: 'Failed to process request',
      details: error.message 
    }, { status: 500 });
  }
}