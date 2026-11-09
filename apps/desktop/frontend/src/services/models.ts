import { backendClient } from './backend';
import type { ModelDiscoveryResponse } from '../types/conversation';
import type { APIEnvelope } from '../types/contracts';

export class ModelService {
  constructor(private client = backendClient) {}

  async listModels(): Promise<ModelDiscoveryResponse> {
    const res = await this.client.fetch<APIEnvelope<ModelDiscoveryResponse>>('/api/v1/models');
    if (res.data) {
      return res.data;
    }
    return {
      models: [],
      errors: {},
      discovered_at: new Date().toISOString(),
    };
  }
}

export const modelService = new ModelService();
