// Minimal typing for the parts of the Plotly basic bundle this app uses.
declare module "plotly.js-basic-dist-min" {
  const Plotly: {
    newPlot(element: HTMLElement, data: object[], layout: object, config: object): Promise<unknown>;
    purge(element: HTMLElement): void;
  };
  export default Plotly;
}
