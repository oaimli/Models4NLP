#! -*- coding: utf-8 -*-
# Text modeling using vae, to find valid encoder and decoder

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from keras.layers import *
from keras.models import Model
from keras.losses import mse
from keras import backend as K
from keras.callbacks import Callback
from keras.optimizers import Adam, SGD

import numpy as np
import random
import pickle

from sklearn import metrics
from sklearn.utils.linear_assignment_ import linear_assignment
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

import sys
sys.path.append('../../')
from miaoli.utils.load_data import load_data
from miaoli.utils.get_embeddings import generate_batch_train_data_mem_vae_x, get_test_embedding_mem_vae
from miaoli.utils.evaluation import plot_2d
from miaoli.DeNVTC.encoder import encoder_net_cnn
from miaoli.DeNVTC.decoder import decoder_net_cnn


# parameters
dataset = "20ng"
batch_size = 32
latent_dim = 500
epochs = 1000


# reparameterization trick
# instead of sampling from Q(z|X), sample eps = N(0,I)
# z = z_mean + sqrt(var)*eps
def sampling(args):
    """Reparameterization trick by sampling fr an isotropic unit Gaussian.

    # Arguments:
        args (tensor): mean and log of variance of Q(z|X)

    # Returns:
        z (tensor): sampled latent vector
    """

    z_mean, z_log_var = args
    batch = K.shape(z_mean)[0]
    dim = K.int_shape(z_mean)[1]
    # by default, random_normal has mean=0 and std=1.0
    epsilon = K.random_normal(shape=(batch, dim))
    return z_mean + K.exp(0.5 * z_log_var) * epsilon


# data load
train_set, test_set, word_embedding_dict, data_characteristics, word_index_dict, embedding_matrix = load_data(dataset, split_count=1000)
X_test, Y_test = get_test_embedding_mem_vae(random.sample(train_set, 1000), data_characteristics["words_dim"],
                                            data_characteristics["embedding_dim"],
                                            word_embedding_dict,
                                            data_characteristics["num_classes"])
with open('x_test.pkl', 'wb') as out:
    pickle.dump(X_test, out)


# VAE model = encoder + decoder
# build encoder model
inputs = Input(shape=(data_characteristics["words_dim"] * data_characteristics["embedding_dim"], ), name='encoder_input')
h = encoder_net_cnn(inputs, data_characteristics)
z_mean = Dense(latent_dim, name='z_mean')(h)
z_mean = Dropout(0.5)(z_mean)
z_log_var = Dense(latent_dim, name='z_log_var')(h)
z_log_var = Dropout(0.5)(z_log_var)

# use reparameterization trick to push the sampling out as input
# note that "output_shape" isn't necessary with the TensorFlow backend
z = Lambda(sampling, output_shape=(latent_dim,), name='z')([z_mean, z_log_var])

# instantiate encoder model
encoder = Model(inputs, [z_mean, z_log_var, z], name='encoder')
encoder.summary()

# build decoder model
latent_inputs = Input(shape=(latent_dim,), name='z_sampling')
embedding_inputs = Input(shape=(data_characteristics["words_dim"] * data_characteristics["embedding_dim"], ), name='encoder_input')
outputs = decoder_net_cnn(latent_inputs, data_characteristics, embedding_inputs)

# instantiate decoder model
decoder = Model([latent_inputs, embedding_inputs], outputs, name='decoder')
decoder.summary()

# instantiate VAE model
outputs = decoder([encoder(inputs)[2], inputs])
vae = Model(inputs, outputs, name='vae')


# VAE loss = mse_loss or xent_loss + kl_loss
reconstruction_loss = mse(inputs, outputs)
reconstruction_loss *= (data_characteristics["words_dim"]*data_characteristics["embedding_dim"])
kl_loss = 1 + z_log_var - K.square(z_mean) - K.exp(z_log_var)
kl_loss = K.sum(kl_loss, axis=-1)
kl_loss *= -0.5
vae_loss = K.mean(reconstruction_loss + kl_loss)
vae.add_loss(vae_loss)
vae.compile(optimizer=Adam(lr=0.001))
vae.summary()

import os
# classifier_weights_dir = "weights/classification_20ng_weights.h5y"
# if os.path.exists(classifier_weights_dir):
#     vae.load_weights(classifier_weights_dir, by_name=True)
vae_weights_dir = "weights/deconv_lvm_20ng_weights.h5y"


class EpochCallback(Callback):
    def on_epoch_begin(self, epoch, logs=None):
        if epoch>0:
            vae.save_weights(vae_weights_dir)
            encoder_re = encoder.predict(X_test)
            print("z_mean", encoder_re[0])
            print("z_log_var", encoder_re[1])

            x_train_encoded = encoder_re[2]
            decoder_re = decoder.predict([x_train_encoded, X_test])
            with open('decoder_output.pkl', 'wb') as out:
                pickle.dump(decoder_re, out)

            x_train_encoded = PCA(n_components=2).fit_transform(x_train_encoded)
            plot_2d(x_train_encoded, Y_test, epoch)
            y_train_pred = KMeans(n_clusters=data_characteristics["num_classes"], init='k-means++', max_iter=100, n_init=20,
                verbose=0).fit(x_train_encoded).labels_
            H, C, VM = metrics.homogeneity_completeness_v_measure(Y_test, y_train_pred)

            y = np.array(Y_test)
            y_pred = np.array(y_train_pred)
            D = max(y_pred.max(), y.max()) + 1
            w = np.zeros((D, D), dtype=np.int64)
            for i in range(y_pred.size):
                w[y_pred[i], y[i]] += 1
            ind = linear_assignment(w.max() - w)
            ACC = sum([w[i, j] for i, j in ind]) * 1.0 / y_pred.size

            print(" Epoch", epoch + 1, "ACC", ACC, "H", H, "C", C, "VM", VM)
epoch_callback = EpochCallback()


# fit model
vae.fit_generator(
    generate_batch_train_data_mem_vae_x(train_set, batch_size, data_characteristics["words_dim"],
                                        data_characteristics["embedding_dim"], word_embedding_dict,
                                        data_characteristics["num_classes"]),
    steps_per_epoch= data_characteristics["all_data_count"] // batch_size,
    epochs=epochs,
    verbose=1,
    callbacks=[epoch_callback],
    validation_data=None,
    validation_steps=None,
    class_weight=None,
    max_queue_size=3,
    workers=1,
    use_multiprocessing=True,
    shuffle=True,
    initial_epoch=0)

