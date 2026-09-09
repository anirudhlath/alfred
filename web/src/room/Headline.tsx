/** 26/500/−0.02em, top-left of the header. `pickHeadline` decides the words. */
export function Headline({ text }: { text: string }) {
  return <h1 className="t-headline">{text}</h1>;
}
