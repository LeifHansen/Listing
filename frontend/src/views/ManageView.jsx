import { Tags } from "lucide-react";
import { motion } from "framer-motion";
import { useApp } from "@/store";
import { PageHeader } from "@/components/ui/PageHeader";
import { ListingsView } from "./ListingsView";
import { Workflow } from "./NewListing";

/* The Manage screen: everything the seller already has, and nothing about
   making a new one. It is the second half of what the old Sell tab was --
   the listings manager, which used to live below an upload box and a drafts
   grid and so started halfway down a page nobody had scrolled.

   Thin on purpose. The manager owns its own tabs, filters and controls
   (ListingsView); this adds the page header and the one rule the split
   needed: an open listing renders HERE rather than sending the seller to the
   List tab. Tapping a live listing on Manage and being dropped onto the
   photo uploader would be a worse answer than the merged screen ever gave. */
export function ManageView({ search = "" }) {
  const { session } = useApp();

  // Same gate NewListing uses, for the same reason: a session IS the editor.
  // Both tabs render it so a listing opens, and closes, where you already are.
  if (session) return <Workflow />;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className="flex flex-col gap-5"
    >
      <PageHeader
        icon={Tags}
        title="Manage"
        subtitle="Everything you're selling, in one place."
      />
      <ListingsView search={search} />
    </motion.div>
  );
}
