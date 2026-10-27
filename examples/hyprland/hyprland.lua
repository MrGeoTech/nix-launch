-- Hyprland (Lua config). Open the launcher, and give rofi's layer surface
-- the glass treatment: blur behind the translucent box (ignore_alpha keeps
-- the blur inside the rounded corners) and dim the rest of the desktop.

hl.bind("SUPER + SPACE", hl.dsp.exec_cmd("rofi -show apps"))

hl.layer_rule({
  name = "glass-rofi",
  match = { namespace = "^rofi$" },
  blur = true,
  ignore_alpha = 0.01,
  dim_around = true,
})
