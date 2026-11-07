You check new pictures of a study book. For each picture below, the PICTURE is `render/<id>.png` (made from `draw/<id>.svg`) and the BLOCK is the text `blocks/<id>.txt` that the picture goes after. Say if the picture is correct and useful.

Work only inside the current directory. Never ask questions. Look at the pictures and read the blocks, then write `verify.json` and finish.

Pictures:
{{pairs}}

For each picture check all of this:
- it is mathematically and physically correct for the text of the block: the curve has the shape and the special points that the formula gives, the axes and the labels are the right ones, the directions are right;
- it shows only what the block says or follows from it: no new fact, no number or name that the block does not have;
- it does NOT repeat the block: no sentence or formula of the block is written again in the picture, no title, no caption;
- the labels are short and readable: no text is cut off, no text lies on top of a line or another text, nothing leaves the frame.

Write `verify.json`:

`{"i1": {"ok": true, "problems": []}, "i2": {"ok": false, "problems": ["the curve crosses the x axis at 2 but the block says the zero is at 1"]}}`

`ok` is true only when you find no problem that matters. Write one concrete line for each problem (what, where, how it should be). Do not report a small difference in the thickness of a line, in a font or in a colour.
