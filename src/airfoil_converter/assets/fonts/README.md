# Bundled faces

Drop the five files here and the window uses them; leave the folder empty and it
falls back to Segoe UI and Consolas, which is what the design says a build
without them must look right on.

```
LibreFranklin-Regular.ttf   LibreFranklin-Medium.ttf   LibreFranklin-SemiBold.ttf
JetBrainsMono-Regular.ttf   JetBrainsMono-Medium.ttf
```

Both families are under the SIL Open Font License, so they may be shipped
inside the executable. `theme.register_bundled_fonts` registers whatever is
here privately to the process before Tk opens a window, and unregisters it on
the way out; nothing is installed system-wide.
