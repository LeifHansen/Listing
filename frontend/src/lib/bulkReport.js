/* What a bulk run says about the listings it left alone.
 *
 * "• Nike hoodie: Nobody is watching that listing yet." — one line per
 * listing a run skipped or could not finish, in the server's own words. A
 * count on its own ("1 skipped") is a button that did something and will not
 * say what: the seller pressed it, read "1 skipped", and had no way to find
 * out what was wrong. Three at most, and the rest counted, which is what a
 * toast has room for. Shared by every screen that reports a bulk job — the
 * dashboard's suggestion groups and the Manage tab's ticked actions. */
export const reasonLines = (rows) => {
  const lines = rows.slice(0, 3).map(
    (r) => `• ${r.title || "A listing"}: ${r.message}`);
  const more = rows.length - lines.length;
  if (more > 0) lines.push(`• …and ${more} more`);
  return lines;
};
