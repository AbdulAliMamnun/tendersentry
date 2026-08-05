import { Board } from "@/components/product/Board";
import { Compliance } from "@/components/product/Compliance";
import { Discovery } from "@/components/product/Discovery";

/** Slug to page body. A product without an entry here fails the build, not the visitor. */
export const PRODUCT_BODIES: Record<string, () => React.JSX.Element> = {
  discovery: Discovery,
  compliance: Compliance,
  board: Board,
};
