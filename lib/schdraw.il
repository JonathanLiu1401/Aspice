; aspice drawing primitives for hand-drawn schematics (load in the live CIW or a batch Virtuoso).
; They do not decide anything: you choose where each part goes and which terminals each wire joins.
; Works with any PDK or library: pin positions are read from the placed instance, never assumed.
;
;   aspTermXY(inst "D")                      -> (x y) centre of that terminal's pin, in sheet coordinates
;   aspPlace(cv "lib" "cell" "M3" "R0" "S" list(x y))
;                                            -> instance placed so that its terminal S sits exactly at (x y)
;   aspPlace(cv "lib" "cell" "M3" "R0" nil list(x y))   -> plain placement, origin at (x y)
;   aspWire(cv m1 "D" m3 "S")                -> wire between two terminals: one straight segment when they are
;                                               aligned, else one bend (vertical first; ?hfirst t for horizontal)
;   aspWirePts(cv list(p1 p2 p3 ...))        -> wire through points you computed (orthogonal segments)
;   aspSnap(xy)                              -> xy rounded to the 1/16 schematic grid (choose on-grid targets;
;                                               pin points are used exactly, so wires always land on the pins)
;
; Typical column (series devices one under the other, straight stack wires):
;   m1 = aspPlace(cv lib "pmos" "M1" "R0" nil list(0 2))
;   m2 = aspPlace(cv lib "nmos" "M2" "R0" "D" list(car(aspTermXY(m1 "D")) cadr(aspTermXY(m1 "D")) - 0.5))
;   aspWire(cv m1 "D" m2 "D")

procedure(aspSnap(xy)
  list(round(car(xy) * 16.0) / 16.0  round(cadr(xy) * 16.0) / 16.0))

procedure(aspTermXY(inst term)
  let((tm pin)
    tm = car(setof(x inst~>master~>terminals x~>name == term))
    unless(tm error("aspTermXY: %s has no terminal %s (terminals: %L)" inst~>name term inst~>master~>terminals~>name))
    pin = car(tm~>pins)
    unless(pin error("aspTermXY: terminal %s of %s has no pin figure" term inst~>name))
    dbTransformPoint(centerBox(pin~>fig~>bBox) list(inst~>xy inst~>orient 1.0))))

procedure(aspPlace(cv lib cell name orient term xy)
  let((master inst p)
    master = dbOpenCellViewByType(lib cell "symbol" nil "r")
    unless(master error("aspPlace: no symbol %s/%s" lib cell))
    inst = dbCreateInst(cv master name if(term xy aspSnap(xy)) orient)
    when(term
      p = aspTermXY(inst term)
      inst~>xy = list(car(inst~>xy) + car(xy) - car(p)  cadr(inst~>xy) + cadr(xy) - cadr(p)))
    inst))

procedure(aspWirePts(cv pts)
  car(schCreateWire(cv "draw" "full" pts 0.0625 0.0625 0.0)))

procedure(aspWire(cv i1 t1 i2 t2 @key (hfirst nil))
  let((a b)
    a = aspTermXY(i1 t1)  b = aspTermXY(i2 t2)
    cond(
      ((abs(car(a) - car(b)) < 1e-6 || abs(cadr(a) - cadr(b)) < 1e-6) aspWirePts(cv list(a b)))
      (hfirst aspWirePts(cv list(a list(car(b) cadr(a)) b)))
      (t      aspWirePts(cv list(a list(car(a) cadr(b)) b))))))
