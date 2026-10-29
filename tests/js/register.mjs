// Lets node load the page's stores outside Agent Zero: the three absolute URLs the host serves are
// mapped to small stand-ins, and the plugin's own absolute URLs to the files on disk.
import { register } from "node:module";
register("./hooks.mjs", import.meta.url);
