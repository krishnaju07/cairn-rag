import { Container, getContainer } from "@cloudflare/containers";

export class CairnContainer extends Container {
  defaultPort = 8100;
  sleepAfter = "30m";

  constructor(ctx, env) {
    super(ctx, env);
    this.envVars = { ALLOWED_HOSTS: env.ALLOWED_HOSTS || "" };
  }
}

export default {
  async fetch(request, env) {
    return getContainer(env.CAIRN, "main").fetch(request);
  },
};
