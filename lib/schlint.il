; schlint.il - is a schematic readable? Counts wire pieces, net labels, labelled stubs and crossings. Draws nothing.
; Load: load("<aspice>/lib/schlint.il"). Python helpers: lib/schlint.py.
;
; aspLint(lib cell [maxShare] [allowNets])  prints one line and returns a property list:
;   ASPLINT <lib> <cell> wires=<n> labels=<n> share=<labels/wires> stubs=<n> crossings=<n> joined=<n> PASS|FAIL
;   labels    net labels, not counting allowNets (supplies, clock, buses you label on purpose) and globals (name!)
;   stubs     wires no longer than 0.5 that carry such a label (a labelled stub is not a visible connection)
;   crossings wire segments of different nets crossing each other
;   joined    nets carrying two or more labels (joined only by name), pins and globals excluded
;   PASS when labels <= maxShare x wires (default 0.34) and stubs <= 25 % of wires.
; Works for any PDK: it only reads wire and label shapes on the schematic "wire" layer.

procedure(aspSegs(cv)
  ; existing wire segments: list of (x1 y1 x2 y2 netName)
  let((out pts net)
    foreach(s cv~>shapes
      when(and(car(s~>lpp) == "wire" member(s~>objType '("line" "path")))
        pts = s~>points net = if(s~>net s~>net~>name "")
        while(cdr(pts)
          out = cons(list(xCoord(car(pts)) yCoord(car(pts)) xCoord(cadr(pts)) yCoord(cadr(pts)) net) out)
          pts = cdr(pts))))
    out))

procedure(aspLint(lib cell @optional (maxShare 0.34) (allowNets '("VDD" "VSS" "GND" "gnd!" "vdd!" "vss!")))
  let((cv segs wires labels stubs labNets pinNets joined cross e h sh res len pass)
    cv = dbOpenCellViewByType(lib cell "schematic" nil "r")
    wires = 0 labels = 0 stubs = 0 cross = 0 e = 0.001
    pinNets = mapcar(lambda((tm) tm~>net~>name) cv~>terminals)
    foreach(s cv~>shapes
      when(car(s~>lpp) == "wire"
        cond(
          (member(s~>objType '("line" "path"))
            wires++
            len = 0.0
            let((p) p = s~>points
              while(cdr(p) len = len + abs(xCoord(car(p)) - xCoord(cadr(p))) + abs(yCoord(car(p)) - yCoord(cadr(p))) p = cdr(p)))
            when(and(len <= 0.5 exists(c s~>children and(c~>objType == "label" !member(c~>theLabel allowNets) !rindex(c~>theLabel "!")))) stubs++))
          (s~>objType == "label"
            unless(or(member(s~>theLabel allowNets) rindex(s~>theLabel "!"))
              labels++
              labNets = cons(s~>theLabel labNets))))))
    ; nets that carry two or more labels and are not pins or globals are joined by labels only
    joined = 0
    foreach(n removeListDuplicates(labNets)
      when(and(length(setof(x labNets x == n)) >= 2 !member(n pinNets) !rindex(n "!")) joined++))
    segs = aspSegs(cv)
    foreach(a segs foreach(b segs
      when(nth(4 a) != nth(4 b)
        h = abs(nth(1 a) - nth(3 a)) < e sh = abs(nth(1 b) - nth(3 b)) < e
        when(and(h !sh nth(0 b) > min(nth(0 a) nth(2 a)) + e nth(0 b) < max(nth(0 a) nth(2 a)) - e
                 nth(1 a) > min(nth(1 b) nth(3 b)) + e nth(1 a) < max(nth(1 b) nth(3 b)) - e)
          cross++))))
    pass = and(wires > 0 labels <= maxShare * wires stubs <= 0.25 * wires)
    printf("ASPLINT %s %s wires=%d labels=%d share=%.2f stubs=%d crossings=%d joined=%d %s\n"
           lib cell wires labels labels / max(1.0 float(wires)) stubs cross joined if(pass "PASS" "FAIL"))
    dbClose(cv)
    list(nil 'wires wires 'labels labels 'share labels / max(1.0 float(wires)) 'stubs stubs 'crossings cross
         'joined joined 'pass pass)))
