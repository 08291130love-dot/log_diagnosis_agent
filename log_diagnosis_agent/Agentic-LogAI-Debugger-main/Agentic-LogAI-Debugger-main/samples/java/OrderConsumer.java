package com.demo;

import org.springframework.stereotype.Component;

@Component
public class OrderConsumer {
    private final InventoryClient inventoryClient;

    public OrderConsumer(InventoryClient inventoryClient) {
        this.inventoryClient = inventoryClient;
    }

    public void consume(OrderMessage message) {
        if (message == null || message.orderId() == null) {
            throw new IllegalArgumentException("order message is invalid");
        }
        inventoryClient.update(message.orderId());
    }
}
