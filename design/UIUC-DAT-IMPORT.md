# Import UIUC airfoil coordinate files

**Feature proposal.** Open a downloaded UIUC `.dat` file in the Airfoil tab, set the physical chord, and preview and export the rib through the existing workflow. This allows CAD and aerodynamic analysis to start from the same section coordinates. The current parser reads airfoil-plotter CSV and XYZ curve files. [Import code](../src/airfoil_converter/parser.py)

Support both common coordinate layouts. SD7037 supplies a title and a continuous contour in Selig order. Clark Y supplies a title, point counts, and separate upper and lower blocks in Lednicer order. Treat the count row as metadata. Both examples express coordinates as fractions of chord. [SD7037 example](https://m-selig.web.engr.illinois.edu/ads/coord/sd7037.dat) · [Clark Y example](https://m-selig.ae.illinois.edu/ads/coord/clarky.dat)

For acceptance, open each unmodified example from the file picker, set a 200 mm chord, and export it. Check the scale and the upper and lower surface orientation against the source, including the original trailing-edge endpoints. Carry the profile name into the rib record.
