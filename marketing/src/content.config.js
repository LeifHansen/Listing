import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const blog = defineCollection({
  loader: glob({ base: "./src/content/blog", pattern: "**/*.md" }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    date: z.coerce.date(),
    author: z.string().default("Thryft Shop"),
    tags: z.array(z.string()).default([]),
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
