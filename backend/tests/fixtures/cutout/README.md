# Cutout regression fixtures

`manifest.json` lists every case the cutout suite runs. Cases here are
**generated** — `builder` names a recipe in `backend/tests/cutout_fixtures.py`
that draws the photo and its exact silhouette — so nothing binary is committed
and the ground truth is exact rather than hand-traced.

Each entry:

| field | meaning |
|---|---|
| `name` | test id, `case/matte` |
| `builder` | recipe in `cutout_fixtures.BUILDERS` (omit when using `photo`/`truth`) |
| `matte` | how the model's matte is wrong: `chewed`, `fringed`, or `soft` |
| `note` | the real-world photo this stands in for |
| `min_retention` | least of the product the cutout may keep |
| `max_hole` | largest solid piece of product that may go missing |
| `max_spill` | most leftover backdrop that may survive |
| `raw_retention` | what the un-refined matte kept, for reference |

`retention` and `max_hole` are the ones that matter. A rough edge is
cosmetic; a missing strap is a listing the seller has to reshoot.

## Running against your own photos

Real photos test the model, which generated fixtures cannot. Put them in a
directory of your own with a `manifest.json` in this same format, using
`photo` and `truth` file names instead of `builder`:

```json
[{ "name": "brown-boots/real", "photo": "boots.jpg", "truth": "boots-mask.png",
   "min_retention": 0.97, "max_hole": 0.02, "max_spill": 0.01 }]
```

`truth` is a black-and-white mask, white where the product is. Then:

```bash
CUTOUT_FIXTURES_DIR=/path/to/your/photos pytest backend/tests/test_cutout_regression.py -q
```

That directory is gitignored (`**/cutout-private/`, and anything you point the
variable at is outside the repo anyway). **Do not commit photos you did not
take, or photos of a seller's inventory without their explicit say-so.**
