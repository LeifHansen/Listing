import rss from "@astrojs/rss";
import { getCollection } from "astro:content";
import { site } from "../lib/site.js";

export async function GET(context) {
  const posts = (await getCollection("blog", ({ data }) => !data.draft)).sort(
    (a, b) => b.data.date - a.data.date,
  );

  const self = new URL("rss.xml", context.site).href;
  const newest = posts[0]?.data.updated ?? posts[0]?.data.date;

  return rss({
    title: `${site.name} blog`,
    description: site.description,
    site: context.site,
    // Match the canonical and sitemap form exactly — no trailing slash.
    trailingSlash: false,
    // Atom's self-link is what a reader (and a feed validator) uses to tell
    // two copies of the same feed apart, and `lastBuildDate` is what stops a
    // poller re-downloading a feed that has not changed. Neither is emitted by
    // default, and both are the difference between a valid feed and a good one.
    xmlns: {
      atom: "http://www.w3.org/2005/Atom",
      // RSS 2.0's own <author> is specified as an email address, and a feed
      // validator says so when it is given a name. dc:creator is the element
      // every real-world feed uses for a byline.
      dc: "http://purl.org/dc/elements/1.1/",
    },
    customData: [
      `<language>en-us</language>`,
      `<atom:link href="${self}" rel="self" type="application/rss+xml"/>`,
      newest ? `<lastBuildDate>${newest.toUTCString()}</lastBuildDate>` : "",
    ].join(""),
    items: posts.map((post) => ({
      title: post.data.title,
      description: post.data.description,
      pubDate: post.data.date,
      link: `/blog/${post.id}`,
      categories: post.data.tags,
      customData: `<dc:creator>${post.data.author}</dc:creator>`,
    })),
  });
}
