You check redrawn diagrams of a study book. For each pair below, the ORIGINAL is the photo `figs/<figure>.jpg` and the REDRAW is the picture `render/<drawing>.png` (made from `draw/<drawing>.svg`). Say if the redraw shows the same thing as the original.

Work only inside the current directory. Never ask questions. Look at the pictures, then write `verify.json` and finish.

Pairs (redraw <- original):
{{pairs}}

For each pair check all of this:
- every element of the original is in the redraw, and the redraw has nothing the original does not show;
- the labels: letters, indices, numbers, units, signs;
- the directions of arrows and forces, the angles, the proportions and the positions of the parts;
- axes, tick values and the units of a graph; the shape and the position of curves and their special points;
- no text is cut off, no text lies on top of a line or another text.

Write `verify.json`:

`{"d1": {"ok": true, "problems": []}, "d2": {"ok": false, "problems": ["the arrow of F2 points down in the original, up in the redraw"]}}`

`ok` is true only when you find no difference that matters. Write one concrete line for each problem (what, where, how it should be). Do not report a small difference in the thickness of a line, in a font or in a colour.
