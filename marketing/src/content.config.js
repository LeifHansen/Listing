import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const blog = defineCollection({
  loader: glob({ base: "./src/content/blog", pattern: "**/*.md" }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    date: z.coerce.date(),
    // Set when a published post is revised. It becomes the post's
    // `dateModified` and its sitemap `lastmod` — which is how a crawler is
    // told to come back and re-read something it has already seen. Without it
    // a rewritten post keeps advertising its original date and may not be
    // re-fetched for months.
    updated: z.coerce.date().optional(),
    author: z.string().default("Thryft Shop"),
    tags: z.array(z.string()).default([]),
    // A post-specific social card, relative to /public. Falls back to the
    // site-wide og-default.png, which is fine but identical for every post.
    image: z.string().optional(),
    imageAlt: z.string().optional(),
    draft: z.boolean().default(false),
  }),
});

const changelog = defineCollection({
  loader: glob({ base: "./src/content/changelog", pattern: "**/*.md" }),
  schema: z.object({
    title: z.string(),
    date: z.coerce.date(),
    // "shipped" is the normal state; "beta" marks something behind a flag.
    state: z.enum(["shipped", "beta"]).default("shipped"),
  }),
});

const legal = defineCollection({
  loader: glob({ base: "./src/content/legal", pattern: "**/*.md" }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    updated: z.coerce.date(),
  }),
});

export const collections = { blog, changelog, legal };
