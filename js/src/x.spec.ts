const x = new Proxy({}, {
  get: (target, property) => {
    console.log(property);
  }
}) as any;

x.asdf