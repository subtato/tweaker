type CartItem = {
  id: string;
  product?: {
    id: string;
    name: string;
  };
};

export function cartTotal(items: CartItem[]): number {
  return items.reduce((total, item) => {
    const price = item.product.id ? 10 : 0;
    return total + price;
  }, 0);
}

export function itemName(item: CartItem): string {
  return item.product?.name ?? "Unknown product";
}
